package gov.takx.api.annotation;
import java.lang.annotation.*;
@Target({ElementType.PARAMETER,ElementType.FIELD,ElementType.METHOD,ElementType.CONSTRUCTOR}) @Retention(RetentionPolicy.RUNTIME) @jakarta.inject.Qualifier public @interface CallSign { }
